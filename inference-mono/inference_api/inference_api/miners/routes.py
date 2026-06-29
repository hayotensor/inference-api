"""Miner self-registration endpoints.

Two-layer auth on POST /miners/register:
  1. an ``rk_*`` service-client of role ``miner`` (``current_miner_client``);
  2. the hotkey self-registration signature, verified ONLY via
     ``talaris_contracts.verify_registration_signature`` (single source of truth).

After both pass, the nonce is replay-guarded, the chain class is (optionally)
checked, and the miner + its model inventory are upserted.
"""

from __future__ import annotations

import uuid

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from talaris_contracts import registration_miner_hash, verify_registration_signature

from inference_api.chain import (
    ChainClient,
    ChainConfigError,
    ChainNode,
    ChainReadError,
    class_meets_minimum,
)
from inference_api.config import settings
from inference_api.db import get_async_session
from inference_api.errors import forbidden, service_unavailable, unauthorized
from inference_api.miners.auth import current_miner_client
from inference_api.miners.replay import claim_registration_nonce
from inference_api.miners.schemas import (
    MinerHeartbeat,
    MinerRegistrationResponse,
    MinerSelfView,
    SelfRegistration,
)
from inference_api.miners.service import (
    MinerRegistryService,
    health_from_registration_health,
)
from inference_api.models import ServiceClient
from inference_api.redis import get_redis

router = APIRouter(prefix="/miners", tags=["miners"])


def get_chain_client() -> ChainClient:
    """Provider for the chain read client (override in tests with a mock)."""
    return ChainClient()


def _self_view(miner, models: list[str]) -> MinerSelfView:
    return MinerSelfView(
        miner_id=miner.id,
        hotkey=miner.hotkey,
        tee_endpoint=miner.tee_endpoint,
        attestation_status=miner.attestation_status,
        attestation_mode=miner.attestation_mode,
        attestation_verified_at=miner.attestation_verified_at,
        attestation_expiry=miner.attestation_expiry,
        health=miner.health,
        last_seen=miner.last_seen,
        subnet_node_id=miner.subnet_node_id,
        chain_class=miner.chain_class,
        miner_hash=miner.miner_hash,
        tls_cert_fingerprint=miner.tls_cert_fingerprint,
        enclave_verify_key=miner.enclave_verify_key,
        models=models,
        capacity=miner.capacity or {},
    )


async def _resolve_chain(reg: SelfRegistration, chain_client: ChainClient) -> ChainNode:
    """Resolve (and gate) on-chain node state. Honors chain_required/min_class."""
    try:
        node = chain_client.get_node_by_hotkey(reg.hotkey)
    except (ChainReadError, ChainConfigError) as exc:
        if settings.chain_required:
            raise service_unavailable("chain_unavailable", "Subnet chain read failed") from exc
        # Chain not required / not configured: fall back to the self-asserted
        # subnet_node_id from the (signed) registration, no class gating.
        return ChainNode(subnet_node_id=reg.subnet_node_id, classification=None)
    if settings.chain_required and not class_meets_minimum(
        node.classification, settings.chain_min_class
    ):
        raise forbidden(
            f"miner chain classification {node.classification!r} is below the required "
            f"minimum {settings.chain_min_class!r}"
        )
    return node


@router.post(
    "/register",
    response_model=MinerRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_miner(
    reg: SelfRegistration,
    session: AsyncSession = Depends(get_async_session),
    miner_client: ServiceClient = Depends(current_miner_client),
    redis_client: redis.Redis = Depends(get_redis),
    chain_client: ChainClient = Depends(get_chain_client),
) -> MinerRegistrationResponse:
    # Layer 2: hotkey self-registration signature (single source of truth).
    if not verify_registration_signature(reg, key_type=settings.registration_key_type):
        raise unauthorized("Invalid self-registration signature")

    # Replay guard on the registration nonce.
    if not await claim_registration_nonce(reg.hotkey, reg.nonce, redis_client):
        raise unauthorized("Registration nonce already used")

    node = await _resolve_chain(reg, chain_client)

    registry = MinerRegistryService(session)
    miner = await registry.upsert_from_registration(
        reg,
        subnet_node_id=node.subnet_node_id if node.subnet_node_id is not None else reg.subnet_node_id,
        chain_class=node.classification,
        miner_hash=registration_miner_hash(reg),
    )
    models = await registry.models_for_miner(miner.id)
    await session.commit()
    return MinerRegistrationResponse(
        miner_id=miner.id,
        hotkey=miner.hotkey,
        attestation_status=miner.attestation_status,
        health=miner.health,
        subnet_node_id=miner.subnet_node_id,
        chain_class=miner.chain_class,
        models=models,
        registered_at=miner.registered_at,
        updated_at=miner.updated_at,
    )


@router.post("/{miner_id}/heartbeat", response_model=MinerSelfView)
async def miner_heartbeat(
    miner_id: uuid.UUID,
    payload: MinerHeartbeat,
    session: AsyncSession = Depends(get_async_session),
    miner_client: ServiceClient = Depends(current_miner_client),
) -> MinerSelfView:
    registry = MinerRegistryService(session)
    miner = await registry.get(miner_id)
    if miner is None:
        raise unauthorized("Unknown miner")
    health = health_from_registration_health(payload.health)
    await registry.record_heartbeat(miner, health=health, capacity=payload.capacity)
    models = await registry.models_for_miner(miner.id)
    await session.commit()
    return _self_view(miner, models)


@router.get("/me", response_model=MinerSelfView)
async def miner_me(
    hotkey: str,
    session: AsyncSession = Depends(get_async_session),
    miner_client: ServiceClient = Depends(current_miner_client),
) -> MinerSelfView:
    registry = MinerRegistryService(session)
    miner = await registry.get_by_hotkey(hotkey)
    if miner is None:
        raise unauthorized("Unknown miner")
    models = await registry.models_for_miner(miner.id)
    return _self_view(miner, models)
