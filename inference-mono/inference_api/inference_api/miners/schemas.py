"""Miner self-registration / heartbeat wire schemas.

The self-registration shapes are RE-EXPORTED from ``talaris_contracts`` — the
single source of truth for the wire format and the signed canonical message. We
do NOT redefine ``SelfRegistration`` / ``HostedModel`` etc. here; only the
heartbeat and response models (which are inference-api-local) are defined.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Re-export the contracts models so callers import them from one place.
from talaris_contracts import (  # noqa: F401
    HostedModel,
    MinerHealth,
    SelfRegistration,
    TeeDescriptor,
)

__all__ = [
    "HostedModel",
    "MinerHealth",
    "SelfRegistration",
    "TeeDescriptor",
    "MinerHeartbeat",
    "MinerRegistrationResponse",
    "MinerSelfView",
]


class MinerHeartbeat(BaseModel):
    """Liveness/capacity ping a registered miner sends periodically."""

    model_config = ConfigDict(extra="allow")

    health: MinerHealth = Field(default_factory=MinerHealth)
    capacity: dict[str, Any] = Field(default_factory=dict)


class MinerRegistrationResponse(BaseModel):
    miner_id: uuid.UUID
    hotkey: str
    attestation_status: str
    health: str
    subnet_node_id: int | None = None
    chain_class: str | None = None
    models: list[str] = Field(default_factory=list)
    registered_at: datetime
    updated_at: datetime


class MinerSelfView(BaseModel):
    miner_id: uuid.UUID
    hotkey: str
    tee_endpoint: str
    attestation_status: str
    attestation_mode: str | None = None
    attestation_verified_at: datetime | None = None
    attestation_expiry: datetime | None = None
    health: str
    last_seen: datetime | None = None
    subnet_node_id: int | None = None
    chain_class: str | None = None
    miner_hash: str | None = None
    tls_cert_fingerprint: str | None = None
    enclave_verify_key: str | None = None
    models: list[str] = Field(default_factory=list)
    capacity: dict[str, Any] = Field(default_factory=dict)
