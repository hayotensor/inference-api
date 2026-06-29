from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from inference_api.security import generate_uuid, utcnow

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class HyphenatedUUID(TypeDecorator):
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PostgresUUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class ServiceClientRole(enum.StrEnum):
    router = "router"
    miner = "miner"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(HyphenatedUUID(), primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)
    hashed_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class APIKeyUsage(Base):
    __tablename__ = "api_key_usage"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=generate_uuid)
    api_key_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("api_keys.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)


class ServiceClient(Base):
    __tablename__ = "service_clients"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    role: Mapped[ServiceClientRole] = mapped_column(
        Enum(ServiceClientRole, name="service_client_role", native_enum=False),
        default=ServiceClientRole.router,
        index=True,
        nullable=False,
    )
    hashed_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class BillingPlan(Base):
    __tablename__ = "billing_plans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=generate_uuid)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    stripe_price_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    monthly_token_allowance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    visible: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), unique=True, index=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("billing_plans.id"), index=True)
    status: Mapped[str] = mapped_column(String(64), default="free", index=True, nullable=False)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CryptoBalanceSnapshot(Base):
    __tablename__ = "crypto_balance_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)
    chain: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    token_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    inference_token_allowance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(512))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)


class ManualTokenAdjustment(Base):
    __tablename__ = "manual_token_adjustments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)


class ModelPricing(Base):
    __tablename__ = "model_pricing"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=generate_uuid)
    model_name: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    token_multiplier: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("1"), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class UsagePeriod(Base):
    __tablename__ = "usage_periods"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    stripe_allowance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    ethereum_erc20_allowance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    substrate_native_allowance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    manual_allowance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_allowance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    used_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    remaining_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class InferenceUsageEvent(Base):
    __tablename__ = "inference_usage_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=generate_uuid)
    usage_period_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("usage_periods.id"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("api_keys.id"), index=True)
    router_client_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("service_clients.id"), index=True
    )
    miner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("miners.id", ondelete="set null"), index=True
    )
    miner_hotkey: Mapped[str | None] = mapped_column(String(128), index=True)
    miner_model_hash: Mapped[str | None] = mapped_column(String(128))
    miner_model_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("miner_models.id", ondelete="set null"), index=True
    )
    miner_receipt_node_id: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    model: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    raw_total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    model_multiplier: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("1"), nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    charged_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="reserved", index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)


# --------------------------------------------------------------------------- #
# SERVING / COORDINATION plane tables. These MIRROR
# api/app/db/models/miner.py; the two definition sites MUST stay in sync.
# --------------------------------------------------------------------------- #


class Miner(Base):
    __tablename__ = "miners"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=generate_uuid)
    hotkey: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    subnet_node_id: Mapped[int | None] = mapped_column(Integer, index=True)
    peer_id: Mapped[str | None] = mapped_column(String(128))
    tee_endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    tls_cert_fingerprint: Mapped[str | None] = mapped_column(String(128))
    enclave_verify_key: Mapped[str | None] = mapped_column(String(128))
    attestation_status: Mapped[str] = mapped_column(String(16), default="pending", index=True, nullable=False)
    attestation_mode: Mapped[str | None] = mapped_column(String(32))
    attestation_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attestation_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    miner_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    chain_class: Mapped[str | None] = mapped_column(String(64))
    health: Mapped[str] = mapped_column(String(16), default="unknown", index=True, nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    capacity: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    usage_chain_head: Mapped[str | None] = mapped_column(String(128))
    usage_count: Mapped[int | None] = mapped_column(Integer)
    usage_total_tokens: Mapped[int | None] = mapped_column(Integer)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class MinerModel(Base):
    __tablename__ = "miner_models"
    __table_args__ = (
        UniqueConstraint(
            "miner_id", "model_id", "model_version", name="uq_miner_models_miner_model_version"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=generate_uuid)
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
    last_advertised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ProvisionedToken(Base):
    __tablename__ = "provisioned_tokens"
    __table_args__ = (
        Index(
            "uq_provisioned_tokens_miner_model_active",
            "miner_id",
            "model_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=generate_uuid)
    miner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("miners.id", ondelete="cascade"), index=True, nullable=False
    )
    model_id: Mapped[str | None] = mapped_column(String(200), index=True)
    key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    encrypted_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    admin_encrypted_token: Mapped[bytes | None] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True, nullable=False)
    provisioned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ModelAllowlist(Base):
    """Platform-approved model artifacts: the canonical (model_id -> approved hash) set the
    attestation verifier checks a miner's attested model_hash against. Mirrors
    ``talaris_attest.claims.ModelAllowlistEntry``."""

    __tablename__ = "model_allowlist"
    __table_args__ = (
        UniqueConstraint(
            "model_id", "model_version", "model_hash", name="uq_model_allowlist_model_version_hash"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=generate_uuid)
    model_id: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(120))
    model_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    args_hash: Mapped[str | None] = mapped_column(String(128))
    gpu_hash: Mapped[str | None] = mapped_column(String(128))
    label: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
