import logging
import uuid

import redis.asyncio as redis
from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_keys.schemas import (
    ALLOWED_API_KEY_SCOPES,
    APIKeyCreate,
    APIKeyCreateResponse,
    APIKeyRead,
    APIKeyUpdate,
    validate_api_key_scopes,
)
from app.core.config import settings
from app.core.errors import forbidden, not_found, unauthorized
from app.core.logging import request_id_ctx
from app.core.security import constant_time_equal, generate_api_key, is_past, keyed_hash, utcnow
from app.db.models.api_key import APIKey
from app.db.models.api_key_usage import APIKeyUsage
from app.db.models.user import User

logger = logging.getLogger(__name__)


class APIKeyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_key(self, user: User, payload: APIKeyCreate) -> APIKeyCreateResponse:
        if not (user.is_verified or user.phone_verified_at):
            raise forbidden("Verify an email address or phone number before creating API keys")
        validate_api_key_scopes(payload.scopes)
        full_key, prefix, last_four = generate_api_key(payload.environment.value)
        db_key = APIKey(
            user_id=user.id,
            name=payload.name,
            environment=payload.environment,
            prefix=prefix,
            last_four=last_four,
            hashed_key=keyed_hash(full_key),
            scopes=payload.scopes,
            expires_at=payload.expires_at,
            rate_limit_per_minute=(
                payload.rate_limit_per_minute or settings.api_key_default_rate_limit_per_minute
            ),
        )
        self.session.add(db_key)
        await self.session.flush()
        return APIKeyCreateResponse(
            key=full_key,
            **APIKeyRead.model_validate(db_key).model_dump(),
        )

    async def list_keys(self, user: User) -> list[APIKey]:
        result = await self.session.execute(
            select(APIKey).where(APIKey.user_id == user.id).order_by(APIKey.created_at.desc())
        )
        return list(result.scalars())

    async def get_key(self, user: User, key_id: uuid.UUID) -> APIKey:
        db_key = await self.session.get(APIKey, key_id)
        if db_key is None or db_key.user_id != user.id:
            raise not_found("API key not found")
        return db_key

    async def update_key(self, user: User, key_id: uuid.UUID, payload: APIKeyUpdate) -> APIKey:
        db_key = await self.get_key(user, key_id)
        update_data = payload.model_dump(exclude_unset=True)
        if "scopes" in update_data and update_data["scopes"] is not None:
            validate_api_key_scopes(update_data["scopes"])
        for field, value in update_data.items():
            setattr(db_key, field, value)
        self.session.add(db_key)
        return db_key

    async def revoke_key(self, user: User, key_id: uuid.UUID) -> APIKey:
        db_key = await self.get_key(user, key_id)
        if db_key.revoked_at is None:
            db_key.revoked_at = utcnow()
            self.session.add(db_key)
        return db_key

    async def authenticate_key(self, raw_key: str, redis_client: redis.Redis | None = None) -> APIKey:
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
        await self.enforce_rate_limit(db_key, redis_client)
        db_key.last_used_at = utcnow()
        self.session.add(db_key)
        return db_key

    async def enforce_rate_limit(self, db_key: APIKey, redis_client: redis.Redis | None) -> None:
        if db_key.rate_limit_per_minute <= 0 or redis_client is None:
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
    ) -> None:
        self.session.add(
            APIKeyUsage(
                api_key_id=db_key.id,
                user_id=db_key.user_id,
                endpoint=request.url.path,
                method=request.method,
                status_code=status_code,
                request_id=request_id_ctx.get(),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )

    async def usage_summary(self, db_key: APIKey) -> dict[str, int | uuid.UUID]:
        result = await self.session.execute(
            select(
                func.count(APIKeyUsage.id),
                func.coalesce(func.sum(APIKeyUsage.input_tokens), 0),
                func.coalesce(func.sum(APIKeyUsage.output_tokens), 0),
            ).where(APIKeyUsage.api_key_id == db_key.id)
        )
        requests, input_tokens, output_tokens = result.one()
        return {
            "api_key_id": db_key.id,
            "requests": int(requests),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
        }
