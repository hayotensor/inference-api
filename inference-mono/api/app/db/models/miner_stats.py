"""Miner statistics: the platform's first-hand record of who served what.

Three tables, one per stage of a deliberately one-way pipeline:

``miner_request_stats``
    Append-only, one row per forward ATTEMPT (success or failure). Written on the
    request path. This is raw evidence and is never edited except to flip
    ``verified`` once the enclave's signed receipt chain corroborates the row.

``miner_epoch_stats``
    The per-``(epoch, hotkey, model)`` aggregate, written ONCE when an epoch is
    finalized. Derived data, kept as a table rather than recomputed so the numbers
    behind a published snapshot can always be audited after the fact.

``miner_epoch_snapshots``
    The signed artifact served to validators, stored verbatim.

WHY THE PIPELINE IS ONE-WAY (consensus-safety)
----------------------------------------------
Validators submit on-chain score sets that must match EXACTLY
(``compare_consensus_data``). They can only do that if they all score from
identical input, which is why a finalized epoch is immutable: the artifact is
built once, persisted, and re-served byte-for-byte forever. Re-aggregating an
already-finalized epoch — even to "fix" it — would hand different validators
different numbers and split consensus. Never mutate a finalized row; if the
aggregation is wrong, publish a corrected LATER epoch.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import generate_uuid, utcnow
from app.db.base import Base


class MinerRequestStat(Base):
    """One forward attempt against one miner — the atomic unit of miner statistics.

    A row is written for EVERY attempt, including failures: a miner that accepted
    routing and then failed to serve is exactly the signal scoring needs, and the
    old failover loop threw that information away.

    ``epoch`` is the subnet epoch the attempt fell in, stamped at write time. It is
    nullable because the platform must never drop a request just because the chain
    RPC was briefly unreachable — an unstamped row simply cannot be scored.

    ``verified`` starts False and is flipped by the receipts collector once this
    ``request_id`` is found in the enclave's signed usage chain with matching token
    counts. Only verified tokens feed scoring, so neither the platform alone nor the
    miner alone can inflate a miner's reward.
    """

    __tablename__ = "miner_request_stats"
    __table_args__ = (
        # The aggregation query: everything for one epoch, grouped per miner+model.
        Index("ix_miner_request_stats_epoch_hotkey", "epoch", "miner_hotkey"),
        # The reconciliation lookup: find the attempt a receipt row corresponds to.
        Index("ix_miner_request_stats_miner_request", "miner_id", "request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    epoch: Mapped[int | None] = mapped_column(Integer, index=True)
    miner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("miners.id", ondelete="set null"), index=True
    )
    # Denormalized on purpose: the hotkey is the identity validators score by, and it
    # must survive the miner row being deleted or re-registered.
    miner_hotkey: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    subnet_node_id: Mapped[int | None] = mapped_column(Integer)
    miner_model_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("miner_models.id", ondelete="set null"), index=True
    )
    model_id: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(120))
    model_hash: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(128), index=True)
    outcome: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    ttft_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )


class MinerEpochStat(Base):
    """Finalized per-(epoch, hotkey, model) aggregate. Write once, never update."""

    __tablename__ = "miner_epoch_stats"
    __table_args__ = (
        UniqueConstraint(
            "epoch",
            "miner_hotkey",
            "model_id",
            "model_version",
            name="uq_miner_epoch_stats_epoch_miner_model",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    epoch: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    miner_hotkey: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    subnet_node_id: Mapped[int | None] = mapped_column(Integer)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    # "" (not NULL) for a versionless model: it is part of a UNIQUE key, and NULLs do
    # not compare equal in SQL, so a NULL here would let duplicate rows through.
    model_version: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    requests_ok: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requests_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    verified_prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    verified_completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    verified_total_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    latency_p50_ms: Mapped[int | None] = mapped_column(Integer)
    latency_p95_ms: Mapped[int | None] = mapped_column(Integer)
    # The enclave's cumulative usage anchor at finalization, carried so a validator
    # can cross-check it against what its own attestation probe observed.
    tee_usage_count_end: Mapped[int | None] = mapped_column(BigInteger)
    tee_usage_chain_head_end: Mapped[str | None] = mapped_column(String(128))
    finalized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class MinerEpochSnapshot(Base):
    """The signed epoch artifact, stored so every read returns identical bytes.

    ``payload_json`` is the serialized ``MinerEpochStatsArtifact``. Serving the
    stored JSON rather than rebuilding it is what makes the endpoint deterministic
    across API restarts, replicas, and later database changes — the signature is
    only valid for the exact bytes that were signed.
    """

    __tablename__ = "miner_epoch_snapshots"

    epoch: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    signature: Mapped[str] = mapped_column(String(256), nullable=False)
    signer_pubkey: Mapped[str | None] = mapped_column(String(128))
    entry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    finalized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )
