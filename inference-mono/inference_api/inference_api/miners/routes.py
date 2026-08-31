"""Miner self-registration endpoints.

Two-layer auth on POST /miners/register:
  1. an ``rk_*`` service-client of role ``miner`` (``current_miner_client``);
  2. the hotkey self-registration signature, verified ONLY via
     ``talaris_contracts.verify_registration_signature`` (single source of truth).

After both pass, the endpoint is SSRF-checked, the nonce is replay-guarded,
the chain class is (optionally) checked, and the miner + its model inventory
are upserted — and the calling service-client is BOUND to that miner
(``miners.auth.bind_client_to_miner``).

Every OTHER miner-scoped route (heartbeat, ``/me``) enforces that binding via
``miners.auth.require_bound_miner`` — ``current_miner_client`` alone only
proves "some valid miner-role token", not WHICH miner the caller may act as.
See ``miners.auth`` for the full trust-model writeup.
"""

from __future__ import annotations

import uuid

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from talaris_contracts import registration_miner_hash, verify_registration_signature
from talaris_contracts.registration import TeeEndpointError, validate_tee_endpoint

from inference_api.chain import (
    ChainClient,
    ChainConfigError,
    ChainNode,
    ChainReadError,
    class_meets_minimum,
)
from inference_api.config import settings
from inference_api.db import get_async_session
from inference_api.errors import bad_request, forbidden, service_unavailable, unauthorized
from inference_api.miners.auth import (
    bind_client_to_miner,
    current_miner_client,
    require_bound_miner,
)
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

    # SSRF guard: reject an endpoint the platform must not dial (cloud metadata,
    # loopback/private ranges in production, etc.) BEFORE it is ever persisted or
    # provisioned. Same check applies to each per-model advertised endpoint, since
    # the provisioner dials those too. See talaris_contracts.registration for the
    # policy and its app_env-derived / operator-opt-in default.
    try:
        validate_tee_endpoint(reg.tee_endpoint, app_env=settings.app_env)
        for hosted_model in reg.hosted_models:
            if hosted_model.tee_endpoint:
                validate_tee_endpoint(hosted_model.tee_endpoint, app_env=settings.app_env)
    except TeeEndpointError as exc:
        raise bad_request("unsafe_tee_endpoint", str(exc)) from exc

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
    # Bind this (already hotkey-signature-verified) service-client to the miner
    # it just registered, so heartbeat/`/me` calls from it are authorized for
    # THIS miner only. See miners.auth for the trust-model writeup.
    await bind_client_to_miner(redis_client, miner_client.id, miner.id)
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
    redis_client: redis.Redis = Depends(get_redis),
) -> MinerSelfView:
    registry = MinerRegistryService(session)
    miner = await registry.get(miner_id)
    if miner is None:
        raise unauthorized("Unknown miner")
    # Ownership check: this service-client must be the one that registered
    # THIS miner (see miners.auth) — otherwise any miner-role token could
    # heartbeat (and so mutate health/capacity for) an unrelated miner.
    await require_bound_miner(redis_client, client_id=miner_client.id, miner_id=miner.id)
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
    redis_client: redis.Redis = Depends(get_redis),
) -> MinerSelfView:
    registry = MinerRegistryService(session)
    miner = await registry.get_by_hotkey(hotkey)
    if miner is None:
        raise unauthorized("Unknown miner")
    # Ownership check: this service-client must be the one that registered
    # THIS miner (see miners.auth) — otherwise any miner-role token could read
    # another miner's private state (enclave_verify_key, tee_endpoint, ...).
    await require_bound_miner(redis_client, client_id=miner_client.id, miner_id=miner.id)
    models = await registry.models_for_miner(miner.id)
    return _self_view(miner, models)
