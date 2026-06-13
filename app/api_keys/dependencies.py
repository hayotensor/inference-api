from collections.abc import Callable

import redis.asyncio as redis
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_keys.service import APIKeyService
from app.core.errors import unauthorized
from app.core.redis import get_redis
from app.db.models.api_key import APIKey
from app.db.session import get_async_session

api_key_bearer = HTTPBearer(auto_error=False)


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
