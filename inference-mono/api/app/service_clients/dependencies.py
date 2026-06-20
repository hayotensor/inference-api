from __future__ import annotations

import redis.asyncio as redis
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import unauthorized
from app.core.redis import get_redis
from app.db.models.service_client import ServiceClient, ServiceClientRole
from app.db.session import get_async_session
from app.service_clients.service import ServiceClientService

service_client_bearer = HTTPBearer(auto_error=False)


async def raw_service_token_from_request(
    credentials: HTTPAuthorizationCredentials | None = Depends(service_client_bearer),
) -> str:
    if credentials and credentials.scheme.lower() == "bearer" and credentials.credentials.startswith("rk_"):
        return credentials.credentials
    raise unauthorized("Service token required")


async def current_router_client(
    request: Request,
    raw_token: str = Depends(raw_service_token_from_request),
    session: AsyncSession = Depends(get_async_session),
    redis_client: redis.Redis = Depends(get_redis),
) -> ServiceClient:
    client = await ServiceClientService(session).authenticate_token(
        raw_token,
        redis_client,
        required_role=ServiceClientRole.router,
    )
    request.state.service_client = client
    return client
