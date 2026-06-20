import logging
import time
from typing import Any

from fastapi import Request
from fastapi_users.jwt import decode_jwt
from jwt import PyJWTError

from app.core.config import settings
from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)

_in_memory_revoked_jtis: dict[str, float] = {}


def extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return decode_jwt(
            token,
            settings.jwt_secret.get_secret_value(),
            [settings.jwt_audience],
            algorithms=["HS256"],
        )
    except PyJWTError:
        return None


def _remember_revoked_jti(jti: str, expires_at: int) -> None:
    _in_memory_revoked_jtis[jti] = float(expires_at)


def _is_memory_revoked(jti: str) -> bool:
    now = time.time()
    expired = [key for key, expires_at in _in_memory_revoked_jtis.items() if expires_at <= now]
    for key in expired:
        _in_memory_revoked_jtis.pop(key, None)
    return _in_memory_revoked_jtis.get(jti, 0) > now


async def revoke_jti(jti: str, expires_at: int) -> None:
    ttl = max(1, int(expires_at - time.time()))
    try:
        redis = get_redis_client()
        await redis.set(f"revoked-access-token:{jti}", "1", ex=ttl)
    except Exception:
        logger.exception("auth.token_revocation_store_unavailable", extra={"jti": jti})
        if settings.app_env == "production" and not settings.token_revocation_fail_open:
            raise
        _remember_revoked_jti(jti, int(time.time()) + ttl)


async def is_jti_revoked(jti: str) -> bool:
    if _is_memory_revoked(jti):
        return True
    try:
        redis = get_redis_client()
        return bool(await redis.exists(f"revoked-access-token:{jti}"))
    except Exception:
        logger.exception("auth.token_revocation_check_unavailable", extra={"jti": jti})
        if settings.app_env == "production" and not settings.token_revocation_fail_open:
            return True
        return False


async def revoke_access_token(token: str) -> bool:
    payload = decode_access_token(token)
    if not payload:
        return False
    jti = payload.get("jti")
    expires_at = payload.get("exp")
    if not jti or not expires_at:
        return False
    await revoke_jti(str(jti), int(expires_at))
    return True
