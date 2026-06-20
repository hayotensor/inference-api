import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_ctx
from app.db.models.audit_log import AuditLog

_SENSITIVE_METADATA_KEYS = {
    "access_token",
    "api_key",
    "client_secret",
    "code",
    "id_token",
    "key",
    "otp",
    "password",
    "refresh_token",
    "reset_token",
    "secret",
    "signature",
    "stripe_secret",
    "token",
}
_SENSITIVE_METADATA_PARTS = ("password", "secret", "signature", "otp")


def _client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host


def _safe_metadata_key(key: str) -> bool:
    normalized = key.lower()
    if normalized in _SENSITIVE_METADATA_KEYS:
        return False
    return not any(part in normalized for part in _SENSITIVE_METADATA_PARTS)


def _sanitize_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_metadata(item) if _safe_metadata_key(str(key)) else "[redacted]"
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_metadata(item) for item in value]
    return value


async def write_audit_log(
    session: AsyncSession,
    action: str,
    *,
    user_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    request: Request | None = None,
    actor: str | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    result: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=user_id,
            actor_user_id=actor_user_id,
            action=action,
            actor=actor,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            result=result,
            request_id=request_id_ctx.get(),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent") if request else None,
            metadata_=_sanitize_metadata(metadata or {}),
        )
    )
