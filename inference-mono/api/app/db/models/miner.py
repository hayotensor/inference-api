from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import generate_uuid, utcnow
from app.db.base import Base


class Miner(Base):
    """A TEE-backed miner that has self-registered with the platform.

    ``hotkey`` is the miner's PUBLIC key hex (the registration ``hotkey``);
    ``sha256(bytes.fromhex(hotkey))`` equals the attestation ``miner`` field which
    binds a registration to its enclave attestation.
    """

    __tablename__ = "miners"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    hotkey: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    subnet_node_id: Mapped[int | None] = mapped_column(Integer, index=True)
    peer_id: Mapped[str | None] = mapped_column(String(128))
    tee_endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    tls_cert_fingerprint: Mapped[str | None] = mapped_column(String(128))
    enclave_verify_key: Mapped[str | None] = mapped_column(String(128))
    attestation_status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True, nullable=False
    )
    attestation_mode: Mapped[str | None] = mapped_column(String(32))
    attestation_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attestation_expiry: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    miner_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    chain_class: Mapped[str | None] = mapped_column(String(64))
    health: Mapped[str] = mapped_column(String(16), default="unknown", index=True, nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    capacity: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # Usage rollback / monotonicity tracking (read from the enclave /stats chain head).
    usage_chain_head: Mapped[str | None] = mapped_column(String(128))
    usage_count: Mapped[int | None] = mapped_column(Integer)
    usage_total_tokens: Mapped[int | None] = mapped_column(Integer)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    models: Mapped[list[MinerModel]] = relationship(
        "MinerModel", back_populates="miner", cascade="all, delete-orphan"
    )
    tokens: Mapped[list[ProvisionedToken]] = relationship(
        "ProvisionedToken", back_populates="miner", cascade="all, delete-orphan"
    )


class MinerModel(Base):
    """A model a miner advertises as hosted (mirrors a registration ``HostedModel``)."""

    __tablename__ = "miner_models"
    __table_args__ = (
        UniqueConstraint(
            "miner_id", "model_id", "model_version", name="uq_miner_models_miner_model_version"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    miner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("miners.id", ondelete="cascade"), index=True, nullable=False
    )
    model_id: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(120))
    model_hash: Mapped[str | None] = mapped_column(String(128))
    # Per-model-enclave serving + attestation state (one single-model enclave per row,
    # fronted by the miner's SNI proxy). Null falls back to the parent Miner row.
    tee_endpoint: Mapped[str | None] = mapped_column(String(512))
    tls_cert_fingerprint: Mapped[str | None] = mapped_column(String(128))
    enclave_verify_key: Mapped[str | None] = mapped_column(String(128))
    attestation_status: Mapped[str] = mapped_column(String(16), default="pending", index=True, nullable=False)
    attestation_mode: Mapped[str | None] = mapped_column(String(32))
    attestation_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attestation_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    usage_chain_head: Mapped[str | None] = mapped_column(String(128))
    usage_count: Mapped[int | None] = mapped_column(Integer)
    usage_total_tokens: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="loaded", nullable=False)
    loaded: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_advertised_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    miner: Mapped[Miner] = relationship("Miner", back_populates="models")


class ProvisionedToken(Base):
    """An inference credential the platform sealed into a miner's enclave.

    Only one row per miner may be ``active`` at a time (enforced by a partial
    unique index on PostgreSQL; SQLite keeps the rows but the service rotates the
    previous active row to ``superseded`` so the invariant holds logically).
    """

    __tablename__ = "provisioned_tokens"
    __table_args__ = (
        # Partial unique: at most one ACTIVE token per miner. A partial index lets
        # any number of revoked/superseded rows coexist. Supported on both
        # PostgreSQL (postgresql_where) and SQLite (sqlite_where).
        Index(
            "uq_provisioned_tokens_miner_model_active",
            "miner_id",
            "model_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=generate_uuid
    )
    miner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("miners.id", ondelete="cascade"), index=True, nullable=False
    )
    model_id: Mapped[str | None] = mapped_column(String(200), index=True)
    key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    encrypted_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    admin_encrypted_token: Mapped[bytes | None] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True, nullable=False)
    provisioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    miner: Mapped[Miner] = relationship("Miner", back_populates="tokens")
