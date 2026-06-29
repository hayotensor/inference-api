"""Miner service-client auth dependency.

Mirrors ``inference_api.service_clients.current_router_client`` exactly, but
requires ``ServiceClientRole.miner``. This is the FIRST of the two-layer
registration auth (the second layer is the hotkey self-registration signature,
verified ONLY via ``talaris_contracts.verify_registration_signature``).
"""

from __future__ import annotations

import redis.asyncio as redis
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.db import get_async_session
from inference_api.errors import unauthorized
from inference_api.models import ServiceClient, ServiceClientRole
from inference_api.redis import get_redis
from inference_api.service_clients import ServiceClientService

miner_client_bearer = HTTPBearer(auto_error=False)


async def raw_miner_token_from_request(
    credentials: HTTPAuthorizationCredentials | None = Depends(miner_client_bearer),
) -> str:
    if (
        credentials
        and credentials.scheme.lower() == "bearer"
        and credentials.credentials.startswith("rk_")
    ):
        return credentials.credentials
    raise unauthorized("Miner service token required")


async def current_miner_client(
    request: Request,
    raw_token: str = Depends(raw_miner_token_from_request),
    session: AsyncSession = Depends(get_async_session),
    redis_client: redis.Redis = Depends(get_redis),
) -> ServiceClient:
    client = await ServiceClientService(session).authenticate_token(
        raw_token,
        redis_client,
        required_role=ServiceClientRole.miner,
    )
    request.state.service_client = client
    return client
