from __future__ import annotations

import logging
import uuid

import redis.asyncio as redis
from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import not_found, unauthorized
from app.core.security import (
    constant_time_equal,
    generate_router_token,
    is_past,
    keyed_hash,
    utcnow,
)
from app.db.models.service_client import ServiceClient, ServiceClientRole
from app.service_clients.schemas import (
    ServiceClientCreate,
    ServiceClientCreateResponse,
    ServiceClientRead,
    ServiceClientsResponse,
)

logger = logging.getLogger(__name__)


class ServiceClientService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_client(self, payload: ServiceClientCreate) -> ServiceClientCreateResponse:
        token, prefix, last_four = generate_router_token()
        client = ServiceClient(
            name=payload.name,
            role=payload.role,
            prefix=prefix,
            last_four=last_four,
            hashed_token=keyed_hash(token),
            expires_at=payload.expires_at,
            rate_limit_per_minute=(
                payload.rate_limit_per_minute or settings.router_default_rate_limit_per_minute
            ),
        )
        self.session.add(client)
        await self.session.flush()
        return ServiceClientCreateResponse(
            token=token,
            **ServiceClientRead.model_validate(client).model_dump(),
        )

    async def list_clients(
        self,
        *,
        limit: int,
        offset: int,
        role: ServiceClientRole | None = None,
        status: str | None = None,
    ) -> ServiceClientsResponse:
        statement = select(ServiceClient)
        if role is not None:
            statement = statement.where(ServiceClient.role == role)
        now = utcnow()
        if status == "active":
            statement = statement.where(
                ServiceClient.revoked_at.is_(None),
                or_(ServiceClient.expires_at.is_(None), ServiceClient.expires_at > now),
            )
        elif status == "revoked":
            statement = statement.where(ServiceClient.revoked_at.is_not(None))
        elif status == "expired":
            statement = statement.where(
                ServiceClient.revoked_at.is_(None),
                ServiceClient.expires_at.is_not(None),
                ServiceClient.expires_at <= now,
            )
        total = await self._count(statement)
        result = await self.session.execute(
            statement.order_by(ServiceClient.created_at.desc()).limit(limit).offset(offset)
        )
        return ServiceClientsResponse(
            data=[ServiceClientRead.model_validate(client) for client in result.scalars()],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def revoke_client(self, client_id: uuid.UUID) -> ServiceClient:
        client = await self.session.get(ServiceClient, client_id)
        if client is None:
            raise not_found("Service client not found")
        if client.revoked_at is None:
            client.revoked_at = utcnow()
            self.session.add(client)
            await self.session.flush()
        return client

    async def authenticate_token(
        self,
        raw_token: str,
        redis_client: redis.Redis | None = None,
        *,
        required_role: ServiceClientRole = ServiceClientRole.router,
    ) -> ServiceClient:
        token_hash = keyed_hash(raw_token)
        result = await self.session.execute(
            select(ServiceClient).where(ServiceClient.hashed_token == token_hash)
        )
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

    async def enforce_rate_limit(
        self, client: ServiceClient, redis_client: redis.Redis | None
    ) -> None:
        if client.rate_limit_per_minute <= 0 or redis_client is None:
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
                    detail={
                        "code": "rate_limit_exceeded",
                        "message": "Service client rate limit exceeded",
                    },
                )
        except HTTPException:
            raise
        except Exception:
            logger.exception("service_client.rate_limit_error", extra={"client_id": str(client.id)})
            if not settings.rate_limit_fail_open:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "rate_limit_unavailable",
                        "message": "Rate limiter unavailable",
                    },
                ) from None

    async def _count(self, statement) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        return int(result.scalar_one())
