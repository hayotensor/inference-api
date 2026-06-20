from __future__ import annotations

import logging

import redis.asyncio as redis
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.config import settings
from inference_api.db import get_async_session
from inference_api.errors import unauthorized
from inference_api.models import ServiceClient, ServiceClientRole
from inference_api.redis import get_redis
from inference_api.security import constant_time_equal, is_past, keyed_hash, utcnow

logger = logging.getLogger(__name__)
service_client_bearer = HTTPBearer(auto_error=False)


class ServiceClientService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def authenticate_token(
        self,
        raw_token: str,
        redis_client: redis.Redis | None = None,
        *,
        required_role: ServiceClientRole = ServiceClientRole.router,
    ) -> ServiceClient:
        token_hash = keyed_hash(raw_token)
        result = await self.session.execute(select(ServiceClient).where(ServiceClient.hashed_token == token_hash))
        client = result.scalar_one_or_none()
        if client is None:
            raise unauthorized("Invalid service token")
        if not constant_time_equal(client.hashed_token, token_hash):
            raise unauthorized("Invalid service token")
        if client.role != required_role:
            raise unauthorized("Invalid service token")
        if client.revoked_at is not None:
            raise unauthorized("Service token has been revoked")
        if client.expires_at is not None and is_past(client.expires_at):
            raise unauthorized("Service token has expired")
        await self.enforce_rate_limit(client, redis_client)
        client.last_used_at = utcnow()
        self.session.add(client)
        return client

    async def enforce_rate_limit(self, client: ServiceClient, redis_client: redis.Redis | None) -> None:
        if not settings.rate_limit_enabled or client.rate_limit_per_minute <= 0 or redis_client is None:
            return
        bucket = int(utcnow().timestamp() // 60)
        redis_key = f"service-client-rate:{client.id}:{bucket}"
        try:
            count = await redis_client.incr(redis_key)
            if count == 1:
                await redis_client.expire(redis_key, 70)
            if count > client.rate_limit_per_minute:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={"code": "rate_limit_exceeded", "message": "Service client rate limit exceeded"},
                )
        except HTTPException:
            raise
        except Exception:
            logger.exception("service_client.rate_limit_error", extra={"client_id": str(client.id)})
            if not settings.rate_limit_fail_open:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "rate_limit_unavailable", "message": "Rate limiter unavailable"},
                ) from None


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
