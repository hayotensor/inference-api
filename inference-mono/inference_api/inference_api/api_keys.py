from __future__ import annotations

import logging
from collections.abc import Callable

import redis.asyncio as redis
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.config import settings
from inference_api.db import get_async_session
from inference_api.errors import forbidden, unauthorized
from inference_api.logging import request_id_ctx
from inference_api.models import APIKey, APIKeyUsage
from inference_api.redis import get_redis
from inference_api.schemas import APIKeyUsageSummary
from inference_api.security import constant_time_equal, is_past, keyed_hash, utcnow

logger = logging.getLogger(__name__)

ALLOWED_API_KEY_SCOPES = frozenset({"models:read", "inference:write", "usage:read"})

api_key_bearer = HTTPBearer(auto_error=False)


class APIKeyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def authenticate_key(
        self,
        raw_key: str,
        redis_client: redis.Redis | None = None,
        *,
        apply_rate_limit: bool = True,
    ) -> APIKey:
        token_hash = keyed_hash(raw_key)
        result = await self.session.execute(select(APIKey).where(APIKey.hashed_key == token_hash))
        db_key = result.scalar_one_or_none()
        if db_key is None:
            raise unauthorized("Invalid API key")
        if not constant_time_equal(db_key.hashed_key, token_hash):
            raise unauthorized("Invalid API key")
        if db_key.revoked_at is not None:
            raise unauthorized("API key has been revoked")
        if db_key.expires_at is not None and is_past(db_key.expires_at):
            raise unauthorized("API key has expired")
        if apply_rate_limit:
            await self.enforce_rate_limit(db_key, redis_client)
        db_key.last_used_at = utcnow()
        self.session.add(db_key)
        return db_key

    async def enforce_rate_limit(self, db_key: APIKey, redis_client: redis.Redis | None) -> None:
        if not settings.rate_limit_enabled or db_key.rate_limit_per_minute <= 0 or redis_client is None:
            return
        bucket = int(utcnow().timestamp() // 60)
        redis_key = f"api-key-rate:{db_key.id}:{bucket}"
        try:
            count = await redis_client.incr(redis_key)
            if count == 1:
                await redis_client.expire(redis_key, 70)
            if count > db_key.rate_limit_per_minute:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={"code": "rate_limit_exceeded", "message": "API key rate limit exceeded"},
                )
        except HTTPException:
            raise
        except Exception:
            logger.exception("api_key.rate_limit_error", extra={"api_key_id": str(db_key.id)})
            if not settings.rate_limit_fail_open:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "rate_limit_unavailable", "message": "Rate limiter unavailable"},
                ) from None

    def require_scopes(self, db_key: APIKey, required_scopes: list[str]) -> None:
        scopes = set(db_key.scopes or [])
        unsupported = sorted(scopes - ALLOWED_API_KEY_SCOPES)
        if unsupported:
            raise forbidden(f"API key contains unsupported scopes: {', '.join(unsupported)}")
        missing = [scope for scope in required_scopes if scope not in scopes]
        if missing:
            raise forbidden(f"API key is missing required scopes: {', '.join(missing)}")

    async def record_usage(
        self,
        db_key: APIKey,
        request: Request,
        *,
        status_code: int = 200,
        input_tokens: int = 0,
        output_tokens: int = 0,
        request_id: str | None = None,
    ) -> None:
        self.session.add(
            APIKeyUsage(
                api_key_id=db_key.id,
                user_id=db_key.user_id,
                endpoint=request.url.path,
                method=request.method,
                status_code=status_code,
                request_id=request_id or request_id_ctx.get(),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )

    async def usage_summary(self, db_key: APIKey) -> APIKeyUsageSummary:
        result = await self.session.execute(
            select(
                func.count(APIKeyUsage.id),
                func.coalesce(func.sum(APIKeyUsage.input_tokens), 0),
                func.coalesce(func.sum(APIKeyUsage.output_tokens), 0),
            ).where(APIKeyUsage.api_key_id == db_key.id)
        )
        requests, input_tokens, output_tokens = result.one()
        return APIKeyUsageSummary(
            api_key_id=db_key.id,
            requests=int(requests),
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
        )


async def raw_api_key_from_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(api_key_bearer),
) -> str:
    header_key = request.headers.get("X-API-Key")
    if header_key:
        return header_key
    if credentials and credentials.scheme.lower() == "bearer" and credentials.credentials.startswith("sk_"):
        return credentials.credentials
    raise unauthorized("API key required")


async def current_api_key(
    request: Request,
    raw_key: str = Depends(raw_api_key_from_request),
    session: AsyncSession = Depends(get_async_session),
    redis_client: redis.Redis = Depends(get_redis),
) -> APIKey:
    service = APIKeyService(session)
    db_key = await service.authenticate_key(raw_key, redis_client)
    request.state.api_key = db_key
    return db_key


def require_api_key(scopes: list[str] | None = None) -> Callable[..., APIKey]:
    required_scopes = scopes or []

    async def dependency(
        request: Request,
        db_key: APIKey = Depends(current_api_key),
        session: AsyncSession = Depends(get_async_session),
    ) -> APIKey:
        service = APIKeyService(session)
        service.require_scopes(db_key, required_scopes)
        request.state.api_key = db_key
        return db_key

    return dependency
